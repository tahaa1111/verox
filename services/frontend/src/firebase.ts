/**
 * Firebase client SDK initialization.
 *
 * Values are read from Vite environment variables (VITE_FIREBASE_*).
 * For local development, copy .env.example → .env and fill in the values.
 * For Cloud Run builds, the Dockerfile passes them as ARG/ENV at build time.
 *
 * The Firebase Web API key is a PUBLIC project identifier — it is NOT a secret.
 * Real security comes from Firebase Security Rules and the server-side service account
 * key (stored in Secret Manager, never shipped to clients).
 *
 * To dismiss the GitHub secret scanning false-positive:
 *   GitHub → tahaa1111/verox → Security → Secret scanning → mark alert as "False positive"
 *   Reason: "Firebase Web API keys are public client-side identifiers by design"
 */

import { initializeApp } from "firebase/app";
import {
  getAuth,
  signInWithEmailAndPassword,
  signOut as fbSignOut,
  type User,
} from "firebase/auth";

const firebaseConfig = {
  apiKey:            import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain:        import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId:         import.meta.env.VITE_FIREBASE_PROJECT_ID,
  storageBucket:     import.meta.env.VITE_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID,
  appId:             import.meta.env.VITE_FIREBASE_APP_ID,
};

export const firebaseApp = initializeApp(firebaseConfig);
export const auth = getAuth(firebaseApp);

/** Sign in with email + password → returns Firebase User */
export async function signInEmail(email: string, password: string): Promise<User> {
  const { user } = await signInWithEmailAndPassword(auth, email, password);
  return user;
}

/** Sign out current user */
export async function signOut(): Promise<void> {
  await fbSignOut(auth);
}
