import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { DisclaimerBanner } from "./components/DisclaimerBanner";
import { NavBar } from "./components/NavBar";
import { CameraPage } from "./pages/CameraPage";
import { ResultsPage } from "./pages/ResultsPage";
import { CorrectionsPage } from "./pages/CorrectionsPage";
import { AdminPage } from "./pages/AdminPage";

/**
 * Two-page SPA:
 *  /           → Camera (live feed + stability countdown → auto-submit)
 *  /results/:jobId → Results (patient · medications · CNAM · confidence)
 *  /corrections/:jobId → Pharmacist correction form
 *  /admin      → Model registry + maintenance toggle
 */
export default function App() {
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
