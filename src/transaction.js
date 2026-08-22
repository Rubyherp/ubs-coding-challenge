const RFC3339 = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$/;

export class ValidationError extends Error {
  constructor(message) {
    super(message);
    this.name = "ValidationError";
  }
}

/** Parse RFC 3339 without losing sub-millisecond precision. */
export function parseTimestamp(value) {
  if (typeof value !== "string") {
    throw new ValidationError("createdAt must be an RFC 3339 string");
  }

  const match = RFC3339.exec(value);
  if (!match) {
    throw new ValidationError("createdAt must be a valid RFC 3339 timestamp");
  }

  const [, yearText, monthText, dayText, hourText, minuteText, secondText, fraction = "", zone] = match;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const hour = Number(hourText);
  const minute = Number(minuteText);
  const second = Number(secondText);

  if (month < 1 || month > 12 || day < 1 || day > daysInMonth(year, month)) {
    throw new ValidationError("createdAt contains an invalid calendar date");
  }
  if (hour > 23 || minute > 59 || second > 59) {
    throw new ValidationError("createdAt contains an invalid time");
  }

  let offsetMinutes = 0;
  if (zone !== "Z") {
    const sign = zone[0] === "+" ? 1 : -1;
    const offsetHours = Number(zone.slice(1, 3));
    const offsetMins = Number(zone.slice(4, 6));
    if (offsetHours > 23 || offsetMins > 59) {
      throw new ValidationError("createdAt contains an invalid UTC offset");
    }
    offsetMinutes = sign * (offsetHours * 60 + offsetMins);
  }

  // Date.UTC treats years 0..99 specially, so set the year explicitly.
  const date = new Date(0);
  date.setUTCFullYear(year, month - 1, day);
  date.setUTCHours(hour, minute, second, 0);
  const epochMilliseconds = date.getTime() - offsetMinutes * 60_000;
  if (!Number.isFinite(epochMilliseconds)) {
    throw new ValidationError("createdAt is outside the supported date range");
  }

  const nanoseconds = BigInt(fraction.padEnd(9, "0"));
  return BigInt(epochMilliseconds) * 1_000_000n + nanoseconds;
}

function daysInMonth(year, month) {
  if (month === 2) {
    const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
    return leap ? 29 : 28;
  }
  return [4, 6, 9, 11].includes(month) ? 30 : 31;
}

export function normalizeTransaction(value, index = 0) {
  const at = `transactions[${index}]`;
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new ValidationError(`${at} must be an object`);
  }

  const txId = requiredString(value.txId, `${at}.txId`);
  const fromUserId = requiredString(value.fromUserId, `${at}.fromUserId`);
  const toUserId = requiredString(value.toUserId, `${at}.toUserId`);

  if (!Object.hasOwn(value, "amount") || typeof value.amount !== "number" || !Number.isFinite(value.amount)) {
    throw new ValidationError(`${at}.amount must be a finite number`);
  }
  if (value.amount < 0) {
    throw new ValidationError(`${at}.amount must not be negative`);
  }

  return Object.freeze({
    txId,
    fromUserId,
    toUserId,
    amount: Object.is(value.amount, -0) ? 0 : value.amount,
    createdAtNs: parseTimestamp(value.createdAt),
    ipAddress: optionalString(value, "ipAddress", at),
    deviceId: optionalString(value, "deviceId", at)
  });
}

function requiredString(value, path) {
  if (typeof value !== "string" || value.length === 0) {
    throw new ValidationError(`${path} must be a non-empty string`);
  }
  if (value.length > 1024) {
    throw new ValidationError(`${path} is too long`);
  }
  return value;
}

function optionalString(object, key, path) {
  if (!Object.hasOwn(object, key) || object[key] === null) {
    return Object.freeze({ present: false, value: null });
  }
  if (typeof object[key] !== "string") {
    throw new ValidationError(`${path}.${key} must be a string when supplied`);
  }
  return Object.freeze({ present: true, value: object[key] });
}

export function sameTransaction(left, right) {
  return left.txId === right.txId
    && left.fromUserId === right.fromUserId
    && left.toUserId === right.toUserId
    && left.amount === right.amount
    && left.createdAtNs === right.createdAtNs
    && sameOptional(left.ipAddress, right.ipAddress)
    && sameOptional(left.deviceId, right.deviceId);
}

function sameOptional(left, right) {
  return left.present === right.present && left.value === right.value;
}
