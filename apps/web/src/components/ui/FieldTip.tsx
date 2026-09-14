import type React from 'react';

interface FieldTipProps {
  content: React.ReactNode;
  side?: 'top' | 'bottom' | 'left' | 'right';
  size?: number;
  className?: string;
}

/**
 * Personal-workspace UI deliberately avoids instructional tooltip chrome.
 * The component remains as a no-op compatibility boundary while screens are
 * progressively simplified; analytical evidence belongs in its own view.
 */
export function FieldTip(_props: FieldTipProps) {
  return null;
}

export default FieldTip;
