// Single source of truth for application_status values.
//
// Dashboard's filter and StatusDropdown each carried their own list and they
// drifted: the filter offered 'Expired' — not a status at all, since expiry is
// the `is_expired` column — so selecting it sent status=Expired, matched zero
// rows, and would have forever. It also omitted 'Phone Screen' and 'Accepted',
// both of which StatusDropdown can set, so a job marked Accepted could never
// be filtered for.
//
// Lives in lib/ rather than beside the component because exporting a constant
// from a component file breaks Fast Refresh (react-refresh/only-export-components).
export const STATUSES = [
  'New',
  'Applied',
  'Phone Screen',
  'Interview',
  'Offer',
  'Rejected',
  'Withdrawn',
  'Accepted',
];

// What the dashboard filter offers: the same vocabulary plus an "any" option.
export const STATUS_FILTER_OPTIONS = ['All', ...STATUSES];
