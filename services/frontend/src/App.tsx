import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { DisclaimerBanner } from "./components/DisclaimerBanner";
import { SubmitPage } from "./pages/SubmitPage";
import { JobTrackerPage } from "./pages/JobTrackerPage";
import { CorrectionsPage } from "./pages/CorrectionsPage";
import { AdminPage } from "./pages/AdminPage";
import { CameraPage } from "./pages/CameraPage";
import { NavBar } from "./components/NavBar";

export default function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-gray-50 flex flex-col">
        <DisclaimerBanner />
        <NavBar />
        <main className="flex-1 container mx-auto px-4 py-6 max-w-5xl">
          <Routes>
            <Route path="/" element={<Navigate to="/submit" replace />} />
            <Route path="/submit" element={<SubmitPage />} />
            <Route path="/jobs/:jobId" element={<JobTrackerPage />} />
            <Route path="/corrections/:jobId" element={<CorrectionsPage />} />
            <Route path="/admin" element={<AdminPage />} />
            <Route path="/camera" element={<CameraPage />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
