import React from 'react';
import WizardShell from '@/components/wizard/WizardShell';

interface MainPageProps {
  onBackToHome?: () => void;
}

export const MainPage: React.FC<MainPageProps> = ({ onBackToHome }) => <WizardShell onBackToHome={onBackToHome} />;

