import { Link } from 'react-router-dom'
import { useAuth } from '../auth/useAuth'
import Card, { CardBody } from '../components/ui/Card'

function Section({ title, children }) {
  return (
    <section className="mb-8">
      <h2 className="text-base font-heading font-bold text-black mb-3 border-b-2 border-stone-200 pb-2">{title}</h2>
      <div className="text-sm text-stone-700 leading-relaxed space-y-3">{children}</div>
    </section>
  )
}

export default function Privacy() {
  const { user, signOut } = useAuth()

  return (
    <div>
      <h1 className="text-2xl font-heading font-bold text-black tracking-tight mb-6">Privacy Policy</h1>

      <Card>
        <CardBody className="p-6 sm:p-8">
          <p className="text-xs text-stone-400 mb-8 font-mono">
            Last updated: March 24, 2026
          </p>

          <Section title="1. What Data We Collect">
            <p>We collect and process the following categories of personal data:</p>
            <ul className="list-disc list-inside space-y-1 ml-2">
              <li><strong className="text-black">Profile information</strong> -- your name, email address, phone number, location, and work authorization status.</li>
              <li><strong className="text-black">Resumes and cover letters</strong> -- documents you upload or that are generated through our tailoring service.</li>
              <li><strong className="text-black">Job search history</strong> -- jobs you have viewed, matched with, or applied to, along with match scores and application statuses.</li>
              <li><strong className="text-black">Search preferences</strong> -- your configured search queries, preferred locations, experience levels, and score thresholds.</li>
              <li><strong className="text-black">Usage data</strong> -- authentication events, timestamps of actions, and consent records.</li>
            </ul>
          </Section>

          <Section title="2. How We Use Your Data">
            <p>Your data is used exclusively to provide and improve our service:</p>
            <ul className="list-disc list-inside space-y-1 ml-2">
              <li><strong className="text-black">AI job matching</strong> -- we use your profile and preferences to score and rank job listings from multiple sources.</li>
              <li><strong className="text-black">Resume tailoring</strong> -- your resume is processed through AI models to customize it for specific job descriptions.</li>
              <li><strong className="text-black">Score tracking</strong> -- we evaluate resumes from three perspectives (ATS, Hiring Manager, Technical Recruiter) and store scores for iterative improvement.</li>
              <li><strong className="text-black">Contact suggestions</strong> -- we identify relevant LinkedIn contacts at target companies to assist your networking.</li>
              <li><strong className="text-black">Email notifications</strong> -- we send summaries of top-matched jobs to your registered email address.</li>
            </ul>
          </Section>

          <Section title="3. Data Storage">
            <p>Your data is stored using the following infrastructure:</p>
            <ul className="list-disc list-inside space-y-1 ml-2">
              <li><strong className="text-black">Supabase (PostgreSQL)</strong> -- user profiles, job records, match scores, application statuses, and consent logs.</li>
              <li><strong className="text-black">Amazon S3</strong> -- generated PDF resumes and cover letters, with presigned URLs that expire after 30 days.</li>
              <li><strong className="text-black">Google Drive</strong> -- permanent shareable links for generated documents, accessible via Google service account.</li>
              <li><strong className="text-black">SQLite cache</strong> -- AI response caches with a 72-hour TTL to reduce redundant API calls.</li>
            </ul>
            <p>
              All data is encrypted in transit (TLS) and at rest. We do not sell, rent, or share your
              personal data with third parties except as necessary to provide the service (e.g., AI
              model providers for resume tailoring).
            </p>
          </Section>

          <Section title="4. Your Rights Under GDPR">
            <p>
              If you are located in the European Economic Area (EEA), you have the following rights
              under the General Data Protection Regulation (GDPR):
            </p>
            <ul className="list-disc list-inside space-y-1 ml-2">
              <li><strong className="text-black">Right of access (Article 15)</strong> -- you can request a copy of all personal data we hold about you.</li>
              <li><strong className="text-black">Right to erasure (Article 17)</strong> -- you can request deletion of your account and all associated data.</li>
              <li><strong className="text-black">Right to data portability (Article 20)</strong> -- you can export your data in a machine-readable format (ZIP archive).</li>
              <li><strong className="text-black">Right to rectification (Article 16)</strong> -- you can update or correct your personal data at any time via the Settings page.</li>
              <li><strong className="text-black">Right to restrict processing (Article 18)</strong> -- you can withdraw your consent at any time.</li>
              <li><strong className="text-black">Right to object (Article 21)</strong> -- you can object to processing of your personal data for specific purposes.</li>
            </ul>
          </Section>

          <Section title="5. How to Exercise Your Rights">
            <p>
              You can exercise your data rights directly through our platform:
            </p>
            <ul className="list-disc list-inside space-y-1 ml-2">
              <li>
                <strong className="text-black">Export your data</strong> -- visit the{' '}
                <Link to="/data-export" className="text-info hover:underline font-bold">
                  Data Export page
                </Link>{' '}
                to download a ZIP archive containing all your personal data.
              </li>
              <li>
                <strong className="text-black">Delete your account</strong> -- visit the{' '}
                <Link to="/data-export" className="text-info hover:underline font-bold">
                  Data Export page
                </Link>{' '}
                to permanently delete your account and all associated data.
              </li>
              <li>
                <strong className="text-black">Update your data</strong> -- visit{' '}
                <Link to="/settings" className="text-info hover:underline font-bold">
                  Settings
                </Link>{' '}
                to modify your profile and preferences.
              </li>
            </ul>
            <p>
              All data export requests are fulfilled immediately. Account deletion requests are
              processed immediately and are irreversible.
            </p>
          </Section>

          <Section title="6. Data Retention">
            <p>
              We retain your personal data for as long as your account is active. AI response caches
              are automatically purged after 72 hours. When you delete your account, all personal
              data is permanently removed from our systems within 30 days, including backups.
            </p>
          </Section>

          <Section title="7. Contact Information">
            <p>
              If you have questions about this privacy policy or wish to exercise your rights, you
              can reach us at:
            </p>
            <ul className="list-none space-y-1 ml-2">
              <li><strong className="text-black">Email:</strong> <a href="mailto:254utkarsh@gmail.com" className="underline hover:no-underline">254utkarsh@gmail.com</a></li>
              <li><strong className="text-black">Data Controller:</strong> NaukriBaba (operated by Utkarsh Singh)</li>
              <li><strong className="text-black">Location:</strong> Dublin, Ireland</li>
            </ul>
            <p>
              You also have the right to lodge a complaint with the Irish Data Protection Commission
              (DPC) at{' '}
              <a
                href="https://www.dataprotection.ie"
                target="_blank"
                rel="noopener noreferrer"
                className="text-info hover:underline font-bold"
              >
                www.dataprotection.ie
              </a>.
            </p>
          </Section>
        </CardBody>
      </Card>
    </div>
  )
}
