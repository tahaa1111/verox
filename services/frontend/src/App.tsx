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
import { HistoryPage } from "./pages/HistoryPage";
import { QueuePage } from "./pages/QueuePage";

/**
 * Firebase auth-gated SPA:
 *  /           → Camera (live feed + stability countdown → auto-submit)
 *  /results/:jobId → Results (patient · medications · CNAM · confidence · raw text)
 *  /corrections/:jobId → Pharmacist correction form
 *  /admin      → Model registry + maintenance (admin users only — requires custom claim)
 *
 * Unauthenticated users see the LoginPage.
 * Admin route is gated on Firebase custom claim { admin: true }.
 * Regular operators only see Camera and Results pages.
 */
export default function App() {
  const { firebaseToken, setFirebaseToken, setIsAdmin, isAdmin } = useStore();
  const [authReady, setAuthReady] = useState(false);

  // Listen for Firebase auth state changes (handles page reload & token refresh)
  useEffect(() => {
    const unsubscribe = onAuthStateChanged(auth, async (user) => {
      if (user) {
        const token = await user.getIdToken();
        setFirebaseToken(token);

        // Read the admin custom claim
        const idTokenResult = await user.getIdTokenResult();
        setIsAdmin(idTokenResult.claims["admin"] === true);

        // Re-fetch token 5 min before the 1-hour expiry
        const expiresIn =
          new Date(idTokenResult.expirationTime).getTime() - Date.now() - 300_000;
        setTimeout(async () => {
          const fresh = await user.getIdToken(true);
          setFirebaseToken(fresh);
          // Also refresh the admin claim after token refresh
          const refreshed = await user.getIdTokenResult(true);
          setIsAdmin(refreshed.claims["admin"] === true);
        }, Math.max(expiresIn, 0));
      } else {
        setFirebaseToken(null);
        setIsAdmin(false);
      }
      setAuthReady(true);
    });
    return unsubscribe;
  }, [setFirebaseToken, setIsAdmin]);

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
            <Route path="/history" element={<HistoryPage />} />
            <Route path="/queue"   element={<QueuePage />} />
            <Route path="/corrections/:jobId" element={<CorrectionsPage />} />

            {/* Admin — only accessible to users with Firebase custom claim { admin: true } */}
            <Route
              path="/admin"
              element={
                isAdmin ? (
                  <AdminPage />
                ) : (
                  <div className="flex flex-col items-center justify-center h-64 gap-3">
                    <svg className="w-12 h-12 text-gray-300" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                        d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                    </svg>
                    <p className="text-gray-500 font-medium">Admin access required</p>
                    <p className="text-gray-400 text-sm">This page is restricted to administrators.</p>
                  </div>
                )
              }
            />

            {/* Catch-all */}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
