FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir fastapi==0.115.0 "uvicorn[standard]==0.30.6"
COPY services/mock_vllm/main.py /app/main.py
EXPOSE 8080
CMD ["python", "main.py"]
