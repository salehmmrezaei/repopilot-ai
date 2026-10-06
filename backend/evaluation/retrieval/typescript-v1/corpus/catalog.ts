/** Options shared by catalog lookups. */
export interface LookupOptions {
  limit: number;
  caseSensitive: boolean;
}
export type RecordId = string | number;
export enum CatalogState { Idle, Loading, Ready }
export class Catalog {
  private rows: string[] = [];
  findByPrefix(prefix: string, options: LookupOptions): string[] {
    const norm = (value: string) => options.caseSensitive ? value : value.toLowerCase();
    return this.rows.filter(row => norm(row).startsWith(norm(prefix))).slice(0, options.limit);
  }
  replace(rows: string[]): void {
    this.rows = [...rows];
  }
}
export const $lookup = (catalog: Catalog, prefix: string): string[] =>
  catalog.findByPrefix(prefix, { limit: 5, caseSensitive: false });
