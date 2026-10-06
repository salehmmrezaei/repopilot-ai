import type { User as Account } from './types';
import * as helpers from './helpers';

/** Request options for the catalog. */
export interface Options {
  readonly limit: number;
  onResult(value: string): void;
}

export type Identifier = string | number;
export enum Status { Ready, Done }

export namespace API {
  export async function fetchUser(id: Identifier): Promise<string> {
    return String(id);
  }
}

/** Holds indexed records. */
@sealed
export class Catalog<T> {
  #value: T;
  constructor(value: T) { this.#value = value; }
  get current(): T { return this.#value; }
  set current(value: T) { this.#value = value; }
  find(id: Identifier): T {
    const normalize = (value: Identifier) => String(value);
    normalize(id);
    return this.#value;
  }
  save = (value: T): void => { this.#value = value; };
}

export const $lookup = async <T>(value: T): Promise<T> => value;
const { key: renamed, other = 1, ...rest } = input;
throw new Error('source must never run');
