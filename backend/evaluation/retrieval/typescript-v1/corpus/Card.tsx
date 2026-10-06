export interface CardProps {
  title: string;
  selected?: boolean;
}
/** Render a selectable catalog card. */
export function Card({ title, selected = false }: CardProps) {
  return <article aria-selected={selected}><h2>{title}</h2></article>;
}
export const EmptyState = () => <p>No catalog entries.</p>;
