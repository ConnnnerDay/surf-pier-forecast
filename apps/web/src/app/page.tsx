import { productStatus } from "@/lib/product-status";

export default function HomePage() {
  return (
    <main>
      <p className="eyebrow">Rewrite scaffold</p>
      <h1>Surf &amp; Pier Forecast</h1>
      <p className="summary">{productStatus()}</p>
    </main>
  );
}
