export default function Home() {
  // sprint 6 CI-failure proof: reference to an undefined variable (tsc/build)
  const proof: string = thisVariableDoesNotExist
  // sprint 6 CI-failure proof: debugger statement (oxlint error-level rule)
  debugger
  return (
    <main style={{ padding: '2rem', maxWidth: 480, margin: '0 auto' }}>
      {proof}
      <h1>Surf & Pier Forecast</h1>
      <p>
        This is the R3 canonical application skeleton — see{' '}
        <code>docs/CANONICAL_ROADMAP.md</code> for the product and
        architecture this will grow into.
      </p>
    </main>
  )
}
