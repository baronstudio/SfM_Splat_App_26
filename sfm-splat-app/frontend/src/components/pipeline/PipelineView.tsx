import React from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

const Step: React.FC<{ name: string, status: string }> = ({ name, status }) => {
    const getStatusColor = () => {
        switch (status) {
            case 'completed': return 'bg-green-500';
            case 'running': return 'bg-blue-500';
            case 'pending': return 'bg-gray-500';
            default: return 'bg-red-500';
        }
    }
    return (
        <div className="flex items-center space-x-4">
            <div className={`w-4 h-4 rounded-full ${getStatusColor()}`}></div>
            <span>{name}</span>
        </div>
    )
}

export const PipelineView: React.FC = () => {
    // Dummy data
    const steps = [
        { name: 'Extract Frames', status: 'completed' },
        { name: 'Compute Matches', status: 'running' },
        { name: 'Align Cameras', status: 'pending' },
        { name: 'Export to 3DGS', status: 'pending' },
    ];

    return (
        <Card className="bg-gray-800 border-gray-700 flex-1">
            <CardHeader>
                <CardTitle>Pipeline</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
                {steps.map(step => <Step key={step.name} {...step} />)}
            </CardContent>
        </Card>
    );
};
