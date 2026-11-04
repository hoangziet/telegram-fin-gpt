import type { ReactNode } from 'react';
import { LanguageContext } from './i18n';

export function LanguageProvider({ children }: { children: ReactNode }) {
  return <LanguageContext value={{ language: 'en' }}>{children}</LanguageContext>;
}
