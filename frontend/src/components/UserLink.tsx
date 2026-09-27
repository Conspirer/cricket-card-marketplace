import { Link } from "react-router-dom";

/** A username that opens that player's profile. */
export function UserLink({ name, className = "" }: { name: string; className?: string }) {
  return (
    <Link
      to={`/u/${encodeURIComponent(name)}`}
      className={`underline-offset-2 hover:text-brass-bright hover:underline ${className}`}
      onClick={(e) => e.stopPropagation()}
    >
      {name}
    </Link>
  );
}
