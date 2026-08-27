import React from 'react';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Separator } from '@/components/ui/separator';
import { Button } from '@/components/ui/button';

interface SettingsDrawerProps {
  open: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  onReset?: () => void;
}

const SettingsDrawer: React.FC<SettingsDrawerProps> = ({
  open,
  onClose,
  title,
  children,
  onReset,
}) => {
  return (
    <Sheet open={open} onOpenChange={(isOpen) => { if (!isOpen) onClose(); }}>
      <SheetContent side="right" className="sm:w-[400px] w-[400px] flex flex-col p-0">
        <SheetHeader className="px-6 py-4 border-b border-slate-700">
          <SheetTitle>{title}</SheetTitle>
        </SheetHeader>
        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
          {children}
        </div>
        {onReset && (
          <>
            <Separator className="bg-slate-700" />
            <div className="px-6 py-4">
              <Button
                variant="outline"
                size="sm"
                onClick={onReset}
                className="w-full border-slate-600 text-slate-400 hover:text-slate-200 hover:bg-slate-800"
              >
                Reset to defaults
              </Button>
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
};

export default SettingsDrawer;
