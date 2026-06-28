'use client';

import { useEffect, useRef } from 'react';
import { useAuthStore } from '@/lib/stores/authStore';

export default function AuthProvider({ children }: { children: React.ReactNode }) {
  const initialized = useRef(false);

  useEffect(() => {
    // Run once, even under React StrictMode's double-invoke in development.
    if (initialized.current) return;
    initialized.current = true;

    useAuthStore.getState().initializeAuth();
  }, []);

  return <>{children}</>;
}
