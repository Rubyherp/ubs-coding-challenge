/** A tiny binary min-heap ordered by transaction timestamp. */
export class TransactionHeap {
  #items = [];

  get size() {
    return this.#items.length;
  }

  peek() {
    return this.#items[0];
  }

  push(item) {
    const items = this.#items;
    items.push(item);
    let index = items.length - 1;
    while (index > 0) {
      const parent = (index - 1) >> 1;
      if (compare(items[parent], item) <= 0) break;
      items[index] = items[parent];
      index = parent;
    }
    items[index] = item;
  }

  pop() {
    const items = this.#items;
    if (items.length === 0) return undefined;
    const root = items[0];
    const tail = items.pop();
    if (items.length === 0) return root;

    let index = 0;
    while (true) {
      const left = index * 2 + 1;
      if (left >= items.length) break;
      const right = left + 1;
      const child = right < items.length && compare(items[right], items[left]) < 0 ? right : left;
      if (compare(items[child], tail) >= 0) break;
      items[index] = items[child];
      index = child;
    }
    items[index] = tail;
    return root;
  }

  clear() {
    this.#items = [];
  }
}

function compare(left, right) {
  if (left.createdAtNs < right.createdAtNs) return -1;
  if (left.createdAtNs > right.createdAtNs) return 1;
  return left.sequence - right.sequence;
}
