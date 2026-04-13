import { Link } from 'react-router-dom'

export default function FinishSetupBanner() {
  return (
    <div className="bg-yellow border-b-2 border-black px-4 py-3 flex items-center justify-between">
      <p className="text-sm font-bold">
        Your profile is incomplete. Complete setup to enable auto-apply.
      </p>
      <Link to="/onboarding" className="text-sm font-bold underline hover:no-underline">
        Finish Setup →
      </Link>
    </div>
  )
}
