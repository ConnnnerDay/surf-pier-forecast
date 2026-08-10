import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

const STEPS = [
  {
    title: 'A quick go/no-go score',
    body: "Every spot gets a 0–100 score from live tide, wind, and wave conditions, plus a plain-language reason why.",
  },
  {
    title: "Best time to fish, today",
    body: 'A headline call-out tells you the best window today, backed by a full hourly timeline underneath.',
  },
  {
    title: "What's biting, and how to catch it",
    body: 'Ranked species for current conditions, with live-bait and lure rig recommendations for each.',
  },
  {
    title: 'Save your spots',
    body: 'Save up to 5 locations and switch between them any time — set your comfort thresholds and target species in your profile.',
  },
]

export function Onboarding() {
  const [step, setStep] = useState(0)
  const navigate = useNavigate()
  const isLast = step === STEPS.length - 1

  return (
    <div className="page">
      <div className="card" style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
        <span className="text-muted">
          Step {step + 1} of {STEPS.length}
        </span>
        <h2>{STEPS[step].title}</h2>
        <p>{STEPS[step].body}</p>
        <div style={{ flex: 1 }} />
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.5rem' }}>
          <button
            className="button button--secondary"
            onClick={() => setStep((s) => Math.max(0, s - 1))}
            disabled={step === 0}
          >
            Back
          </button>
          <button
            className="button"
            onClick={() => (isLast ? navigate('/dashboard') : setStep((s) => s + 1))}
          >
            {isLast ? 'Get started' : 'Next'}
          </button>
        </div>
      </div>
    </div>
  )
}
