export namespace API {
  export function pathForUser(id: string): string {
    return `/users/${encodeURIComponent(id)}`;
  }
  export function isSuccess(status: number): boolean {
    return status >= 200 && status < 300;
  }
}
