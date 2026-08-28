import type { ReactNode } from "react";

export function Panel({
  title,
  right,
  children,
  bodyClass,
  className,
}: {
  title: string;
  right?: ReactNode;
  children: ReactNode;
  bodyClass?: string;
  className?: string;
}) {
  return (
    <section className={"panel" + (className ? " " + className : "")}>
      <header className="panel-h">
        <span className="panel-t">{title}</span>
        {right}
      </header>
      <div className={"panel-b" + (bodyClass ? " " + bodyClass : "")}>{children}</div>
      <i className="hud c-tl" />
      <i className="hud c-tr" />
      <i className="hud c-bl" />
      <i className="hud c-br" />
    </section>
  );
}
