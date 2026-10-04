// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
const { servingStatus } = require('grpc-js-health-check')

// Keep the drain bound below the pod's terminationGracePeriodSeconds.
function createShutdownHandler(server, health, logger, {
  gracePeriodMs = 20_000,
  exit = code => process.exit(code)
} = {}) {
  let shuttingDown = false

  return signal => {
    if (shuttingDown) return
    shuttingDown = true
    health.setStatus('', servingStatus.NOT_SERVING)
    logger.info({ signal }, 'Draining payment gRPC requests')

    let finished = false
    let forced = false
    const finish = code => {
      if (finished) return
      finished = true
      clearTimeout(timeout)
      exit(code)
    }

    const timeout = setTimeout(() => {
      forced = true
      logger.warn('Payment shutdown grace period expired')
      server.forceShutdown()
      finish(1)
    }, gracePeriodMs)

    server.tryShutdown(error => {
      if (error) {
        forced = true
        logger.error({ error }, 'Payment gRPC shutdown failed')
        server.forceShutdown()
      }
      finish(forced ? 1 : 0)
    })
  }
}

module.exports = { createShutdownHandler }
