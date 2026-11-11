import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { UnknownSpoolModal } from '../../components/UnknownSpoolModal';
import type { UnknownSpoolPrompt } from '../../hooks/useUnknownTagPrompt';

const basePrompt: UnknownSpoolPrompt = {
  printer_id: 1,
  ams_id: 0,
  tray_id: 0,
  printer_name: 'X1C',
  ams_label: 'AMS A',
  slot_label: 'Slot 1',
  material: 'PLA',
  color_hex: 'C12E1FFF',
  brand: 'PLA Basic',
  remain: 30,
  reason: 'unknown',
  archived_spool: null,
};

const refilledPrompt: UnknownSpoolPrompt = {
  ...basePrompt,
  remain: 95,
  reason: 'refilled_archived',
  archived_spool: {
    id: 7,
    material: 'PETG',
    subtype: null,
    brand: 'Sunlu',
    color_name: 'Black',
    rgba: '000000FF',
    label_weight: 1000,
    core_weight: 180,
    remaining_pct: 2,
    reused_tag: true,
  },
};

const addButton = () => screen.getByRole('button', { name: /add to inventory|re-add spool/i });

describe('UnknownSpoolModal', () => {
  it('asks which kind of spool it is before anything can be added', () => {
    const onConfirm = vi.fn();
    render(<UnknownSpoolModal prompt={basePrompt} isPending={false} onConfirm={onConfirm} onCancel={vi.fn()} />);

    expect(screen.getByText(/authentic Bambu Lab spool, or an old Bambu tag/i)).toBeInTheDocument();
    expect(addButton()).toBeDisabled();
  });

  it('adds an authentic spool from the tag', () => {
    const onConfirm = vi.fn();
    render(<UnknownSpoolModal prompt={basePrompt} isPending={false} onConfirm={onConfirm} onCancel={vi.fn()} />);

    fireEvent.click(screen.getByRole('radio', { name: /authentic bambu lab spool/i }));
    fireEvent.click(addButton());

    expect(onConfirm).toHaveBeenCalledWith({ reused: false });
  });

  it('asks for the real filament of a reused tag', () => {
    const onConfirm = vi.fn();
    render(<UnknownSpoolModal prompt={basePrompt} isPending={false} onConfirm={onConfirm} onCancel={vi.fn()} />);

    fireEvent.click(screen.getByRole('radio', { name: /reused tag on other filament/i }));
    // A material is required.
    expect(addButton()).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/material/i), { target: { value: 'PETG' } });
    fireEvent.change(screen.getByLabelText(/brand/i), { target: { value: 'Sunlu' } });
    fireEvent.click(addButton());

    expect(onConfirm).toHaveBeenCalledWith(
      expect.objectContaining({ reused: true, material: 'PETG', brand: 'Sunlu', label_weight: 1000 }),
    );
  });

  it('suggests re-adding a refilled archived tag, prefilled from the archived spool', () => {
    const onConfirm = vi.fn();
    render(<UnknownSpoolModal prompt={refilledPrompt} isPending={false} onConfirm={onConfirm} onCancel={vi.fn()} />);

    expect(screen.getByText(/tag back on a new spool/i)).toBeInTheDocument();
    expect(screen.getByText(/Sunlu PETG Black, archived empty \(2% left\)\. It now reads 95%/)).toBeInTheDocument();
    // A refilled tag is a reused one: no "authentic" choice.
    expect(screen.queryByRole('radio', { name: /authentic/i })).not.toBeInTheDocument();

    fireEvent.click(addButton());

    expect(onConfirm).toHaveBeenCalledWith({
      reused: true,
      material: 'PETG',
      subtype: null,
      brand: 'Sunlu',
      color_name: 'Black',
      rgba: '000000FF',
      label_weight: 1000,
      core_weight: 180,
    });
  });

  it('only offers adding from the tag where reused tags are not supported (Spoolman)', () => {
    const onConfirm = vi.fn();
    render(
      <UnknownSpoolModal
        prompt={basePrompt}
        isPending={false}
        reusedTagSupported={false}
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.queryByRole('radio')).not.toBeInTheDocument();
    fireEvent.click(addButton());
    expect(onConfirm).toHaveBeenCalledWith({ reused: false });
  });
});
