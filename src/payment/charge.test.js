// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
const assert = require('node:assert/strict');
const { test, beforeEach, afterEach, mock } = require('node:test');
const { OpenFeature } = require('@openfeature/server-sdk');
const { trace, metrics, propagation, SpanStatusCode } = require('@opentelemetry/api');

// Isolate external telemetry and flagd; card validation and UUID generation
// remain real. Loading charge.js must not start an OTLP logger worker.
const span = {
  setAttribute: mock.fn(), setAttributes: mock.fn(),
  recordException: mock.fn(), setStatus: mock.fn(), end: mock.fn(),
};
const counter = { add: mock.fn() };
const logger = { info: mock.fn() };
const loggerPath = require.resolve('./logger');
const previousLogger = require.cache[loggerPath];
require.cache[loggerPath] = { id: loggerPath, filename: loggerPath, loaded: true, exports: logger };
const tracerMock = mock.method(trace, 'getTracer', () => ({ startSpan: () => span }));
const meterMock = mock.method(metrics, 'getMeter', () => ({ createCounter: () => counter }));
const { charge } = require('./charge');
tracerMock.mock.restore();
meterMock.mock.restore();
if (previousLogger) require.cache[loggerPath] = previousLogger;
else delete require.cache[loggerPath];

beforeEach(() => {
  for (const fn of [...Object.values(span), counter.add, logger.info]) fn.mock.resetCalls();
  mock.method(OpenFeature, 'setProviderAndWait', async () => {});
  mock.method(OpenFeature, 'getClient', () => ({ getNumberValue: async () => 0 }));
  mock.method(propagation, 'getBaggage', () => undefined);
});
afterEach(() => mock.restoreAll());

function request(number = '4111111111111111') {
  return {
    creditCard: {
      creditCardNumber: number,
      creditCardExpirationYear: new Date().getFullYear() + 1,
      creditCardExpirationMonth: 12,
    },
    amount: { units: 12, nanos: 500000000, currencyCode: 'USD' },
  };
}

for (const [type, number] of [['visa', '4111111111111111'], ['mastercard', '5555555555554444']]) {
  test(`accepts a valid ${type}, records a transaction, and ends the span`, async () => {
    const result = await charge(request(number));
    assert.match(result.transactionId, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    assert.deepEqual(counter.add.mock.calls[0].arguments, [1, { 'demo.payment.currency': 'USD' }]);
    assert.equal(logger.info.mock.calls[0].arguments[0].cardType, type);
    assert.equal(logger.info.mock.calls[0].arguments[0].lastFourDigits, number.slice(-4));
    assert.equal(span.end.mock.callCount(), 1);
    assert.equal(span.recordException.mock.callCount(), 0);
  });
}

for (const [name, modify, message] of [
  ['invalid card number', r => { r.creditCard.creditCardNumber = '4111111111111112'; }, /Credit card info is invalid/],
  ['unsupported American Express', r => { r.creditCard.creditCardNumber = '378282246310005'; }, /Only VISA or MasterCard is accepted/],
  ['expired card', r => { r.creditCard.creditCardExpirationYear = new Date().getFullYear() - 1; }, /ending 1111.*expired/],
]) {
  test(`rejects ${name} without counting a transaction`, async () => {
    const r = request();
    modify(r);
    await assert.rejects(charge(r), message);
    assert.equal(counter.add.mock.callCount(), 0);
    assert.equal(logger.info.mock.callCount(), 0);
    assert.equal(span.recordException.mock.callCount(), 1);
    assert.equal(span.setStatus.mock.calls[0].arguments[0].code, SpanStatusCode.ERROR);
    assert.equal(span.end.mock.callCount(), 1);
  });
}

test('accepts a card through its expiration month', async () => {
  const r = request();
  r.creditCard.creditCardExpirationYear = new Date().getFullYear();
  r.creditCard.creditCardExpirationMonth = new Date().getMonth() + 1;
  assert.ok((await charge(r)).transactionId);
});

test('synthetic requests are not charged and preserve the end user ID', async () => {
  mock.method(propagation, 'getBaggage', () => propagation.createBaggage({
    synthetic_request: { value: 'true' }, 'enduser.id': { value: 'test-user' },
  }));
  await charge(request());
  const attributes = Object.fromEntries(span.setAttribute.mock.calls.map(call => call.arguments));
  assert.equal(attributes['demo.payment.charged'], false);
  assert.equal(attributes['user_agent.synthetic.type'], 'test');
  assert.equal(attributes['enduser.id'], 'test-user');
});

test('ordinary requests are marked charged', async () => {
  await charge(request());
  const attributes = Object.fromEntries(span.setAttribute.mock.calls.map(call => call.arguments));
  assert.equal(attributes['demo.payment.charged'], true);
});

test('the failure flag rejects the request before processing payment', async () => {
  mock.method(OpenFeature, 'getClient', () => ({ getNumberValue: async () => 1 }));
  mock.method(Math, 'random', () => 0.25);
  await assert.rejects(charge(request()), /Payment request failed.*Invalid token/);
  assert.equal(counter.add.mock.callCount(), 0);
  assert.equal(span.end.mock.callCount(), 1);
});
