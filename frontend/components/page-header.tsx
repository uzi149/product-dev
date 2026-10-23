export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: React.ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        {eyebrow && (
          <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.16em] text-green">
            {eyebrow}
          </div>
        )}
        <h1 className="text-[clamp(25px,3vw,38px)] font-semibold leading-[1.06] tracking-[-0.045em]">
          {title}
        </h1>
        {description && (
          <p className="mt-2 max-w-[590px] text-[13px] leading-relaxed text-muted-foreground">
            {description}
          </p>
        )}
      </div>
      {actions}
    </div>
  );
}

export function ComingSoon({
  eyebrow,
  title,
  description,
  phase,
}: {
  eyebrow: string;
  title: string;
  description: string;
  phase: string;
}) {
  return (
    <>
      <PageHeader eyebrow={eyebrow} title={title} description={description} />
      <div className="grid min-h-[260px] place-items-center rounded-2xl border border-dashed text-center">
        <div>
          <div className="font-mono text-[10px] uppercase tracking-[0.16em] text-green">
            Not built yet
          </div>
          <p className="mt-2 text-sm text-muted-foreground">This screen is built in {phase}.</p>
        </div>
      </div>
    </>
  );
}
