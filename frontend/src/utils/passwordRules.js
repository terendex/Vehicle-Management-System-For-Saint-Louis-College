/* Password strength rules, shared by every "set a new password" screen.
   These mirror the checks in accounts/views.py ChangePasswordView exactly —
   if the server list changes, change this one with it, or the form will let
   through a password the API then rejects. */

export const PW_RULES = [
  { key: 'length',  label: 'At least 8 characters',         test: (p) => p.length >= 8 },
  { key: 'upper',   label: 'One uppercase letter',          test: (p) => /[A-Z]/.test(p) },
  { key: 'lower',   label: 'One lowercase letter',          test: (p) => /[a-z]/.test(p) },
  { key: 'number',  label: 'One number',                    test: (p) => /[0-9]/.test(p) },
  { key: 'special', label: 'One special character (!@#$…)', test: (p) => /[!@#$%^&*()_+\-=[\]{};'"\\|,.<>/?]/.test(p) },
]

export function pwStrength(pw) {
  if (!pw) return { level: '', score: 0 }
  const passed = PW_RULES.filter(r => r.test(pw)).length
  if (passed <= 1) return { level: 'weak',   score: 1 }
  if (passed === 2) return { level: 'fair',   score: 2 }
  if (passed === 3) return { level: 'good',   score: 3 }
  if (passed === 4) return { level: 'strong', score: 4 }
  return { level: 'excellent', score: 5 }
}

export const STRENGTH_LABELS = {
  weak: 'Weak', fair: 'Fair', good: 'Good', strong: 'Strong', excellent: 'Excellent',
}
