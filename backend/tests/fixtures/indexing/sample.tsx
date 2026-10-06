import type { ReactNode } from 'react';
export interface CardProps { title: string; children?: ReactNode; }
/** Displays a title and children. */
export const Card = ({ title, children }: CardProps) => (
  <article><h2>{title}</h2>{children}</article>
);
export function Container({ children }: CardProps) {
  return <section data-kind="container">{children}</section>;
}
export default function () { return <Card title="Welcome" />; }
const attack = '<script>do_not_execute()</script>';
