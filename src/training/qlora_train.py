"""
QLoRA fine-tuning script for Qwen2.5-VL-7B on Vertex AI Custom Job.
Spec §8.2 component 4.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--training_data_uri", required=True)
    p.add_argument("--output_dir", required=True)
    p.add_argument("--base_model", default="Qwen/Qwen2.5-VL-7B-Instruct")
    p.add_argument("--lora_rank", type=int, default=16)
    p.add_argument("--lora_alpha", type=int, default=32)
    p.add_argument("--lr", type=float, default=5e-5)
    p.add_argument("--epochs", type=int, default=1)
    p.add_argument("--batch_size", type=int, default=1)
    p.add_argument("--grad_accum", type=int, default=8)
    return p.parse_args()


def download_training_data(gcs_uri: str, local_path: str) -> None:
    from google.cloud import storage
    client = storage.Client()
    bucket_name, blob_name = gcs_uri.lstrip("gs://").split("/", 1)
    client.bucket(bucket_name).blob(blob_name).download_to_filename(local_path)
    print(f"Downloaded training data from {gcs_uri} to {local_path}")


def upload_adapter(local_dir: str, gcs_uri: str) -> None:
    import subprocess
    subprocess.run(["gsutil", "-m", "cp", "-r", local_dir, gcs_uri], check=True)
    print(f"Uploaded adapter to {gcs_uri}")


def train(args: argparse.Namespace) -> None:
    from transformers import AutoProcessor, BitsAndBytesConfig, TrainingArguments
    from peft import LoraConfig, get_peft_model, TaskType
    from trl import SFTTrainer
    from datasets import Dataset

    print(f"[qlora_train] Loading model: {args.base_model}")
    print(f"[qlora_train] LoRA rank={args.lora_rank}, alpha={args.lora_alpha}")
    print(f"[qlora_train] LR={args.lr}, epochs={args.epochs}, batch={args.batch_size}, grad_accum={args.grad_accum}")

    # Download training data
    local_data = "/tmp/train.jsonl"
    download_training_data(args.training_data_uri, local_data)

    # Load dataset
    records = [json.loads(line) for line in Path(local_data).read_text().splitlines() if line.strip()]
    dataset = Dataset.from_list(records)
    print(f"[qlora_train] Training samples: {len(dataset)}")

    # 4-bit quantization for T4 / A100
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_use_double_quant=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
    )

    from transformers import Qwen2VLForConditionalGeneration
    model = Qwen2VLForConditionalGeneration.from_pretrained(
        args.base_model,
        quantization_config=bnb_config,
        device_map="auto",
        trust_remote_code=True,
    )
    processor = AutoProcessor.from_pretrained(args.base_model, trust_remote_code=True)

    # LoRA configuration
    lora_config = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=args.lora_rank,
        lora_alpha=args.lora_alpha,
        lora_dropout=0.05,
        bias="none",
        target_modules=["q_proj", "v_proj", "k_proj", "o_proj",
                         "gate_proj", "up_proj", "down_proj"],
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    local_output = "/tmp/lora-adapter"
    training_args = TrainingArguments(
        output_dir=local_output,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        bf16=torch.cuda.is_available() and torch.cuda.get_device_capability()[0] >= 8,
        fp16=torch.cuda.is_available() and torch.cuda.get_device_capability()[0] < 8,
        save_strategy="epoch",
        logging_steps=10,
        report_to="none",
        dataloader_num_workers=0,
    )

    def formatting_func(example):
        messages = example.get("messages", [])
        return processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)

    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=dataset,
        formatting_func=formatting_func,
        max_seq_length=2048,
        packing=False,
    )

    print("[qlora_train] Starting training")
    trainer.train()
    trainer.save_model(local_output)
    print(f"[qlora_train] Adapter saved to {local_output}")

    # Upload to GCS
    upload_adapter(local_output, args.output_dir)
    print(f"[qlora_train] Training complete. Adapter at {args.output_dir}")


if __name__ == "__main__":
    args = parse_args()
    train(args)
