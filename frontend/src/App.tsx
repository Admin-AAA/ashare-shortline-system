import AppShell from "./app/AppShell";
import AuthGate from "./app/AuthGate";

export default function App() {
  return (
    <AuthGate>
      <AppShell />
    </AuthGate>
  );
}
