export function BallSeam({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 60 60" className={className} aria-hidden>
      <circle cx="30" cy="30" r="26" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <path
        d="M17 7.5c6.5 6 6.5 39 0 45M43 7.5c-6.5 6-6.5 39 0 45"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeDasharray="2.2 2"
      />
    </svg>
  );
}

export function CardBack() {
  return (
    <div className="tc-back">
      <div className="tc-back__frame">
        <div className="tc-back__inner">
          <BallSeam className="w-[34cqw]" />
          <div className="text-center">
            <div className="tc-back__word">CREASE</div>
            <div className="tc-back__sub mt-[2cqw]">Series One</div>
          </div>
        </div>
      </div>
    </div>
  );
}
