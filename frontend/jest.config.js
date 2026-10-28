const nextJest = require('next/jest')
const createJestConfig = nextJest({ dir: './' })
module.exports = createJestConfig({
  testEnvironment: 'jsdom',
  setupFilesAfterEnv: ['<rootDir>/jest.setup.js'],
  // Playwright E2E specs live under e2e/ and run via `npm run test:e2e`.
  testPathIgnorePatterns: ['/node_modules/', '/e2e/'],
})
