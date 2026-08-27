import React from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Play, Pause, StopCircle } from 'lucide-react';

export const Controls: React.FC = () => {
    return (
        <Card className="bg-gray-800 border-gray-700">
            <CardHeader>
                <CardTitle>Controls</CardTitle>
            </CardHeader>
            <CardContent className="flex justify-around">
                <Button variant="ghost" size="icon"><Play /></Button>
                <Button variant="ghost" size="icon"><Pause /></Button>
                <Button variant="ghost" size="icon"><StopCircle /></Button>
            </CardContent>
        </Card>
    );
};
