import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ConfirmModal } from './ConfirmModal';
import { getSwatchStyle } from '../utils/colors';
import { MATERIALS, DEFAULT_BRANDS } from './spool-form/constants';
import type { UnknownSpoolAnswer, UnknownSpoolPrompt } from '../hooks/useUnknownTagPrompt';

interface UnknownSpoolModalProps {
  prompt: UnknownSpoolPrompt | null;
  isPending: boolean;
  /** False in Spoolman mode: there the prompt only adds the tag's filament. */
  reusedTagSupported?: boolean;
  onConfirm: (answer: UnknownSpoolAnswer) => void;
  onCancel: () => void;
}

type Kind = 'authentic' | 'reused';

const inputClass =
  'w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:border-bambu-green focus:outline-none';

/** "RRGGBBAA" (or "#RRGGBB") -> "#rrggbb" for a colour input. */
function toColorInput(hex: string | null | undefined): string {
  const clean = (hex ?? '').replace(/^#/, '');
  return /^[0-9a-fA-F]{6}/.test(clean) ? `#${clean.slice(0, 6).toLowerCase()}` : '#808080';
}

/**
 * Asks about a tag Bambuddy has never seen: an authentic Bambu Lab spool
 * (added from the tag's data), or an old tag reused on other filament (the
 * user names the real filament; the tag's data is ignored). For the tag of an
 * empty archived spool that now reads much fuller, it suggests re-adding it,
 * prefilled from the archived spool.
 */
export function UnknownSpoolModal({
  prompt,
  isPending,
  reusedTagSupported = true,
  onConfirm,
  onCancel,
}: UnknownSpoolModalProps) {
  const { t } = useTranslation();
  const refilled = prompt?.reason === 'refilled_archived';
  const archived = prompt?.archived_spool ?? null;

  // A refilled tag is a reused one by definition (a genuine refill brings a
  // new tag), so that answer is preselected. An unknown tag gets no default:
  // the user has to say which it is.
  const [kind, setKind] = useState<Kind | null>(
    !reusedTagSupported ? 'authentic' : refilled ? 'reused' : null,
  );
  const [material, setMaterial] = useState(archived?.material ?? '');
  const [brand, setBrand] = useState(archived?.brand ?? '');
  const [colorName, setColorName] = useState(archived?.color_name ?? '');
  const [color, setColor] = useState(toColorInput(archived?.rgba));
  const [labelWeight, setLabelWeight] = useState(String(archived?.label_weight ?? 1000));

  if (!prompt) return null;

  const location = `${prompt.printer_name} • ${prompt.ams_label} • ${prompt.slot_label}`;
  const swatchStyle = prompt.color_hex ? getSwatchStyle(prompt.color_hex) : undefined;
  // tray_sub_brands usually already names the material ("PLA Basic"); don't repeat it.
  const tagFilament =
    prompt.brand && prompt.material && !prompt.brand.toUpperCase().includes(prompt.material.toUpperCase())
      ? `${prompt.brand} ${prompt.material}`
      : prompt.brand || prompt.material || '—';
  const weight = Number(labelWeight);
  const reusedValid = material.trim() !== '' && Number.isFinite(weight) && weight > 0;
  const canConfirm = kind === 'authentic' || (kind === 'reused' && reusedValid);

  const archivedName = archived
    ? [archived.brand, archived.material, archived.subtype, archived.color_name].filter(Boolean).join(' ')
    : '';

  const confirm = () => {
    if (kind === 'authentic') {
      onConfirm({ reused: false });
    } else if (kind === 'reused' && reusedValid) {
      onConfirm({
        reused: true,
        material: material.trim(),
        subtype: archived?.subtype ?? null,
        brand: brand.trim() || null,
        color_name: colorName.trim() || null,
        rgba: `${color.replace('#', '').toUpperCase()}FF`,
        label_weight: weight,
        core_weight: archived?.core_weight ?? null,
      });
    }
  };

  const materialOptions = material && !MATERIALS.includes(material) ? [material, ...MATERIALS] : MATERIALS;

  const choice = (value: Kind, title: string, desc: string) => (
    <label
      className={`flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors ${
        kind === value
          ? 'border-bambu-green bg-bambu-green/10'
          : 'border-bambu-dark-tertiary bg-bambu-dark-secondary hover:border-bambu-gray'
      }`}
    >
      <input
        type="radio"
        name="unknown-spool-kind"
        className="mt-1 accent-bambu-green"
        checked={kind === value}
        onChange={() => setKind(value)}
        disabled={isPending}
      />
      <span className="min-w-0">
        <span className="block text-white text-sm font-medium">{title}</span>
        <span className="block text-xs text-bambu-gray">{desc}</span>
      </span>
    </label>
  );

  return (
    <ConfirmModal
      title={refilled ? t('inventory.refilledTagTitle') : t('inventory.unknownSpoolTitle')}
      message={
        refilled && archived
          ? t('inventory.refilledTagMessage', {
              location,
              spool: archivedName,
              pct: archived.remaining_pct,
              remain: prompt.remain ?? '?',
            })
          : reusedTagSupported
            ? t('inventory.unknownTagQuestion', { location })
            : t('inventory.unknownSpoolMessage', { location })
      }
      confirmText={refilled ? t('inventory.readdSpool') : t('inventory.addToInventory')}
      cancelText={t('common.cancel')}
      variant="default"
      cardClassName="max-w-lg"
      isLoading={isPending}
      loadingText={t('inventory.addToInventoryPending')}
      confirmDisabled={!canConfirm}
      onConfirm={confirm}
      onCancel={onCancel}
    >
      <div className="space-y-3">
        <div className="flex items-center gap-3 p-3 rounded-lg bg-bambu-dark-secondary border border-bambu-dark-tertiary">
          {swatchStyle && (
            <div
              className="w-8 h-8 rounded-full border border-black/20 flex-shrink-0"
              style={swatchStyle}
              aria-label={prompt.color_hex ?? undefined}
            />
          )}
          <div className="min-w-0 flex-1">
            <p className="text-xs text-bambu-gray">{t('inventory.tagReports')}</p>
            <p className="text-white text-sm font-medium truncate">{tagFilament}</p>
          </div>
          {prompt.remain != null && (
            <span className="text-xs text-bambu-gray whitespace-nowrap">
              {t('inventory.remainPct', { pct: prompt.remain })}
            </span>
          )}
        </div>

        {reusedTagSupported && (
          <div className="space-y-2" role="radiogroup" aria-label={t('inventory.unknownTagKind')}>
            {!refilled &&
              choice('authentic', t('inventory.authenticSpool'), t('inventory.authenticSpoolDesc'))}
            {choice('reused', t('inventory.reusedTagSpool'), t('inventory.reusedTagSpoolDesc'))}
          </div>
        )}

        {kind === 'reused' && (
          <div className="space-y-3 p-3 rounded-lg border border-bambu-dark-tertiary">
            <p className="text-sm text-white font-medium">{t('inventory.realFilament')}</p>
            <div className="grid grid-cols-2 gap-3">
              <label className="block text-xs text-bambu-gray">
                {t('inventory.material')} *
                <select
                  className={`${inputClass} mt-1`}
                  value={material}
                  onChange={(e) => setMaterial(e.target.value)}
                  disabled={isPending}
                >
                  <option value="">—</option>
                  {materialOptions.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block text-xs text-bambu-gray">
                {t('inventory.brand')}
                <input
                  className={`${inputClass} mt-1`}
                  list="unknown-spool-brands"
                  value={brand}
                  onChange={(e) => setBrand(e.target.value)}
                  disabled={isPending}
                />
                <datalist id="unknown-spool-brands">
                  {DEFAULT_BRANDS.map((b) => (
                    <option key={b} value={b} />
                  ))}
                </datalist>
              </label>
              <label className="block text-xs text-bambu-gray">
                {t('inventory.colorName')}
                <input
                  className={`${inputClass} mt-1`}
                  value={colorName}
                  onChange={(e) => setColorName(e.target.value)}
                  disabled={isPending}
                />
              </label>
              <label className="block text-xs text-bambu-gray">
                {t('inventory.color')}
                <input
                  type="color"
                  className="mt-1 w-full h-[38px] bg-bambu-dark border border-bambu-dark-tertiary rounded-lg cursor-pointer"
                  value={color}
                  onChange={(e) => setColor(e.target.value)}
                  disabled={isPending}
                />
              </label>
              <label className="block text-xs text-bambu-gray col-span-2">
                {t('inventory.labelWeight')} (g)
                <input
                  type="number"
                  min={1}
                  className={`${inputClass} mt-1`}
                  value={labelWeight}
                  onChange={(e) => setLabelWeight(e.target.value)}
                  disabled={isPending}
                />
              </label>
            </div>
            <p className="text-xs text-bambu-gray">{t('inventory.reusedTagStartsFull')}</p>
          </div>
        )}
      </div>
    </ConfirmModal>
  );
}
