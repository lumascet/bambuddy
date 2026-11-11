import { Tags } from 'lucide-react';
import { useTranslation } from 'react-i18next';

/**
 * Corner marker on an AMS slot whose spool carries a reused RFID tag: the
 * slot shows the spool's real filament, and this says why it differs from
 * what the printer (and the tag) report.
 */
export function ReusedTagMarker() {
  const { t } = useTranslation();
  return (
    <span
      aria-label={t('ams.reusedTagMarker')}
      title={t('ams.reusedTagMarkerTitle')}
      className="absolute top-0.5 left-0.5 p-px rounded bg-amber-400 text-bambu-dark pointer-events-none leading-none"
    >
      <Tags className="w-2.5 h-2.5" aria-hidden="true" />
    </span>
  );
}
