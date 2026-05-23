import { useEffect, useState } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { onAuthStateChanged } from "firebase/auth";
import { auth } from "./firebase";
import { useStore } from "./store";
import { DisclaimerBanner } from "./components/DisclaimerBanner";
import { NavBar } from "./components/NavBar";
import { LoginPage } from "./pages/LoginPage";
import { CameraPage } from "./pages/CameraPage";
import { ResultsPage } from "./pages/ResultsPage";
import { CorrectionsPage } from "./pages/CorrectionsPage";
import { AdminPage } from "./pages/AdminPage";

/**
 * Firebase auth-gated SPA:
 *  /           → Camera (live feed + stability countdown → auto-submit)
 *  /results/:jobId → Results (patient · medications · CNAM · confidence)
 *  /corrections/:jobId → Pharmacist correction form
 *  /admin      → Model registry + maintenance toggle
 *
 * Unauthenticated users see the LoginPage.
 * Firebase onAuthStateChanged keeps the token fresh on reload.
 */
export default function App() {
  const { firebaseToken, setFirebaseToken } = useStore();
  const [authReady, setAuthReady] = useState(false);

  // Listen for Firebase auth state changes (handles page reload & token refresh)
  useEffect(() => {
    const unsubscribe = onAuthStateChanged(auth, async (user) => {
      if (user) {
        const token = await user.getIdToken();
        setFirebaseToken(token);
        // Re-fetch token 5 min before the 1-hour expiry
        const tokenResult = await user.getIdTokenResult();
        const expiresIn = new Date(tokenResult.expirationTime).getTime() - Date.now() - 300_000;
        setTimeout(async () => {
          const fresh = await user.getIdToken(true);
          setFirebaseToken(fresh);
        }, Math.max(expiresIn, 0));
      } else {
        setFirebaseToken(null);
      }
      setAuthReady(true);
    });
    return unsubscribe;
  }, [setFirebaseToken]);

  // Show nothing while Firebase initializes (avoids login flicker on reload)
  if (!authReady) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="animate-spin rounded-full h-10 w-10 border-2 border-brand-600 border-t-transparent" />
      </div>
    );
  }

  if (!firebaseToken) {
    return <LoginPage onLogin={() => {}} />;
  }

  return (
    <BrowserRouter>
      <div className="min-h-screen bg-gray-50 flex flex-col">
        <DisclaimerBanner />
        <NavBar />
        <main className="flex-1 container mx-auto px-4 py-8 max-w-4xl">
          <Routes>
            <Route path="/" element={<CameraPage />} />
            <Route path="/camera" element={<Navigate to="/" replace />} />
            <Route path="/results/:jobId" element={<ResultsPage />} />
            {/* Legacy job tracker URL — redirect to new results path */}
            <Route path="/jobs/:jobId" element={<Navigate to="/results/:jobId" replace />} />
            <Route path="/corrections/:jobId" element={<CorrectionsPage />} />
            <Route path="/admin" element={<AdminPage />} />
            {/* Catch-all */}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
