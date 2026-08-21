import { Badge, Button, Card, Container, Field } from './components/ui'

export default function Home() {
  return (
    <main>
      <Container>
        <header className="py-10 sm:py-16">
          <p className="text-sm font-medium tracking-wide text-primary uppercase">
            Placeholder identity — see docs/CANONICAL_ROADMAP.md sprint 27
          </p>
          <h1 className="mt-2 text-4xl font-bold text-text sm:text-5xl">
            Surf &amp; Pier Forecast
          </h1>
          <p className="mt-3 max-w-prose text-lg text-text-muted">
            Clear go / no-go fishing forecasts for surf and pier anglers —
            condition data, not just numbers, explained in plain language.
          </p>
        </header>

        <section aria-labelledby="gallery-heading" className="flex flex-col gap-10 pb-16">
          <h2 id="gallery-heading" className="text-2xl font-semibold text-text">
            Design-system gallery
          </h2>

          <div className="flex flex-col gap-3">
            <h3 className="text-lg font-medium text-text">Forecast verdict</h3>
            <p className="text-sm text-text-muted">
              Sprint 32&apos;s traffic-light dashboard headline — color
              reinforces the verdict, the text label always states it.
            </p>
            <div className="flex flex-wrap gap-3">
              <Badge variant="go">Go</Badge>
              <Badge variant="marginal">Marginal</Badge>
              <Badge variant="nogo">No-go</Badge>
              <Badge variant="neutral">Unavailable</Badge>
            </div>
          </div>

          <div className="flex flex-col gap-3">
            <h3 className="text-lg font-medium text-text">Buttons</h3>
            <div className="flex flex-wrap gap-3">
              <Button variant="primary">Primary action</Button>
              <Button variant="secondary">Secondary action</Button>
              <Button variant="ghost">Ghost action</Button>
              <Button variant="primary" href="/">
                Link button
              </Button>
              <Button variant="primary" disabled>
                Disabled
              </Button>
            </div>
          </div>

          <div className="flex flex-col gap-3">
            <h3 className="text-lg font-medium text-text">Cards</h3>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Card>
                <h4 className="font-semibold text-text">Wrightsville Beach, NC</h4>
                <p className="mt-1 text-sm text-text-muted">
                  A card is a bordered surface for grouping related content —
                  a forecast summary, a saved location, a catch-log entry.
                </p>
              </Card>
              <Card>
                <h4 className="font-semibold text-text">Montauk, NY</h4>
                <p className="mt-1 text-sm text-text-muted">
                  Same primitive, any content. Padding and radius come from
                  the shared design tokens in <code>app/globals.css</code>.
                </p>
              </Card>
            </div>
          </div>

          <div className="flex flex-col gap-3">
            <h3 className="text-lg font-medium text-text">Form fields</h3>
            <Card className="max-w-sm">
              <Field label="Location name" placeholder="e.g. Wrightsville Beach" />
              <Field
                label="Email"
                type="email"
                hint="Used for account alerts, never shared."
              />
              <Field
                label="Password"
                type="password"
                error="Password must be at least 8 characters."
              />
            </Card>
          </div>
        </section>
      </Container>
    </main>
  )
}
