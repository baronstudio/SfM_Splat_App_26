import React from 'react';
import { Settings } from 'lucide-react';
import { Button } from '@/components/ui/button';

export const Header: React.FC = () => {
    return (
        <header className="bg-gray-800 p-4 flex justify-between items-center">
            <h1 className="text-2xl font-bold">3DGS Pipeline</h1>
            <Button variant="ghost" size="icon">
                <Settings className="h-6 w-6" />
            </Button>
        </header>
    );
};
