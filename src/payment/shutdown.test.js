// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
const assert = require('node:assert/strict')
const { test, mock } = require('node:test')
const grpc = require('@grpc/grpc-js')
const health = require('grpc-js-health-check')
const { createShutdownHandler } = require('./shutdown')

const encode = value => Buffer.from(JSON.stringify(value))
const decode = buffer => JSON.parse(buffer.toString())
const service = {
  hold: {
    path: '/test.Payment/Hold', requestStream: false, responseStream: false,
    requestSerialize: encode, requestDeserialize: decode,
    responseSerialize: encode, responseDeserialize: decode
  }
}
const Client = grpc.makeGenericClientConstructor(service, 'Payment')
const HealthClient = grpc.makeGenericClientConstructor(health.service, 'Health')

function deferred() {
  let resolve
  const promise = new Promise(complete => { resolve = complete })
  return { promise, resolve }
}

async function runningServer(t) {
  const server = new grpc.Server()
  const status = new health.Implementation({ '': health.servingStatus.SERVING })
  const started = deferred()
  server.addService(health.service, status)
  server.addService(service, { hold: (call, callback) => started.resolve({ call, callback }) })
  const port = await new Promise((resolve, reject) => {
    server.bindAsync('127.0.0.1:0', grpc.ServerCredentials.createInsecure(), (error, port) => {
      if (error) reject(error)
      else resolve(port)
    })
  })
  const address = `127.0.0.1:${port}`
  const client = new Client(address, grpc.credentials.createInsecure())
  const healthClient = new HealthClient(address, grpc.credentials.createInsecure())
  t.after(() => { client.close(); healthClient.close(); server.forceShutdown() })
  const response = await new Promise((resolve, reject) => {
    healthClient.check({ service: '' }, (error, response) => error ? reject(error) : resolve(response))
  })
  assert.equal(response.status, 'SERVING')
  const request = new Promise(resolve => {
    client.hold({}, (error, response) => resolve({ error, response }))
  })
  const active = await started.promise
  return { server, status, request, active }
}

function logger() {
  return { info: mock.fn(), warn: mock.fn(), error: mock.fn() }
}

test('SIGTERM allows an active RPC to finish and stops advertising readiness', { timeout: 5000 }, async t => {
  const { server, status, request, active } = await runningServer(t)
  const setStatus = mock.method(status, 'setStatus')
  t.after(() => setStatus.mock.restore())
  const stopped = deferred()
  const exit = mock.fn(code => stopped.resolve(code))
  const shutdown = createShutdownHandler(server, status, logger(), { gracePeriodMs: 2000, exit })

  shutdown('SIGTERM')
  shutdown('SIGINT')
  assert.equal(exit.mock.callCount(), 0, 'The process must remain alive while the RPC is active')
  assert.equal(setStatus.mock.callCount(), 1, 'Repeated signals must not start another drain')
  assert.deepEqual(setStatus.mock.calls[0].arguments, ['', health.servingStatus.NOT_SERVING])

  active.callback(null, { transactionId: 'completed' })
  const result = await request
  assert.equal(result.error, null)
  assert.deepEqual(result.response, { transactionId: 'completed' })
  assert.equal(await stopped.promise, 0)
  assert.equal(exit.mock.callCount(), 1)
})

test('a stuck RPC is cancelled when the shutdown deadline expires', { timeout: 5000 }, async t => {
  const { server, status, request } = await runningServer(t)
  const stopped = deferred()
  const exit = mock.fn(code => stopped.resolve(code))
  const log = logger()
  const shutdown = createShutdownHandler(server, status, log, { gracePeriodMs: 50, exit })

  shutdown('SIGTERM')
  assert.equal(await stopped.promise, 1)
  const result = await request
  assert.ok(result.error, 'An unfinished RPC must be cancelled after the deadline')
  assert.equal(log.warn.mock.callCount(), 1)
  assert.equal(exit.mock.callCount(), 1, 'Timeout and gRPC callback must not both exit')
})

test('a gRPC shutdown error closes the server and exits once', () => {
  const server = { tryShutdown: callback => callback(new Error('shutdown failed')), forceShutdown: mock.fn() }
  const status = { setStatus: mock.fn() }
  const exit = mock.fn()
  const log = logger()
  createShutdownHandler(server, status, log, { exit })('SIGTERM')
  assert.equal(server.forceShutdown.mock.callCount(), 1)
  assert.equal(log.error.mock.callCount(), 1)
  assert.deepEqual(exit.mock.calls.map(call => call.arguments), [[1]])
})
