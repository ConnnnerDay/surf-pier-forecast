import { Link } from 'react-router-dom'

export function NotFound() {
  return (
    <div className="page">
      <h1>Page not found</h1>
      <Link to="/">Back home</Link>
    </div>
  )
}
