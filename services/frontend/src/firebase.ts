/**
 * Firebase client SDK initialization.
 * The web API key is the public client key visible in the browser bundle —
 * this is expected for Firebase; the actual secret is the service account key
 * which lives only in Secret Manager and is never shipped to clients.
 */

import { initializeApp } from "firebase/app";
import {
  getAuth,
  signInWithEmailAndPassword,
  signOut as fbSignOut,
  type User,
} from "firebase/auth";

const firebaseConfig = {
  apiKey: "AIzaSyAHIyeHbJSfvMxRbgLIsfvwqYRyY3gkawg",
  authDomain: "verox-4dc3f.firebaseapp.com",
  projectId: "verox-4dc3f",
  storageBucket: "verox-4dc3f.firebasestorage.app",
  messagingSenderId: "272258744118",
  appId: "1:272258744118:web:af92c89718758d3531bd97",
};

export const firebaseApp = initializeApp(firebaseConfig);
export const auth = getAuth(firebaseApp);

/** Sign in with email + password → returns Firebase ID token */
export async function signInEmail(email: string, password: string): Promise<User> {
  const { user } = await signInWithEmailAndPassword(auth, email, password);
  return user;
}

/** Sign out current user */
export async function signOut(): Promise<void> {
  await fbSignOut(auth);
}
