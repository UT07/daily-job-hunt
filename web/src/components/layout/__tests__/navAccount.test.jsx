/**
 * Account actions reachable from every screen size.
 *
 * 1. Sign out existed only in the desktop Sidebar (hidden below md, and only
 *    when expanded). MobileNav had Home / Add / More, so on a phone there was
 *    no way to sign out at all.
 * 2. /privacy and /data-export (export, delete account, consent status) were
 *    routed but linked from nowhere in the navigation.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../../auth/useAuth', () => ({ useAuth: vi.fn() }));
vi.mock('../../NotificationBell', () => ({ default: () => null }));

import { useAuth } from '../../../auth/useAuth';
import MobileNav from '../MobileNav';
import Sidebar from '../Sidebar';

let signOut;
beforeEach(() => {
  signOut = vi.fn(async () => {});
  useAuth.mockReturnValue({ user: { id: 'u1', email: 'a@b.c' }, signOut });
});

const inRouter = (ui) => render(<MemoryRouter>{ui}</MemoryRouter>);

describe('MobileNav', () => {
  it('offers sign out', async () => {
    inRouter(<MobileNav />);
    fireEvent.click(screen.getByRole('button', { name: /Sign out/i }));
    await waitFor(() => expect(signOut).toHaveBeenCalled());
  });

  it('shows a failed sign out instead of doing nothing', async () => {
    signOut.mockRejectedValue(new Error('network down'));
    inRouter(<MobileNav />);
    fireEvent.click(screen.getByRole('button', { name: /Sign out/i }));
    expect(await screen.findByRole('button', { name: /Sign out failed: network down/i })).toBeInTheDocument();
  });
});

describe('Settings footer (where mobile "More" lands)', () => {
  it('links to Data & Privacy and the Privacy Policy', async () => {
    const api = await import('../../../api');
    vi.spyOn(api, 'apiGet').mockResolvedValue({});
    const { default: Settings } = await import('../../../pages/Settings');
    render(<Settings />);
    expect(screen.getByRole('link', { name: /Data & Privacy/ })).toHaveAttribute('href', '/data-export');
    expect(screen.getByRole('link', { name: /Privacy Policy/ })).toHaveAttribute('href', '/privacy');
  });
});

describe('Sidebar account section', () => {
  it('links to Data & Privacy and the Privacy Policy', () => {
    inRouter(<Sidebar />);
    expect(screen.getByRole('link', { name: /Data & Privacy/ })).toHaveAttribute('href', '/data-export');
    expect(screen.getByRole('link', { name: /Privacy Policy/ })).toHaveAttribute('href', '/privacy');
  });
});
