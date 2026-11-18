import { realpathSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const require = createRequire(import.meta.url)
const matcher = require('picomatch')

describe('picomatch security backport', () => {
  it('loads the patched parser from every tool that uses picomatch', () => {
    const patchedEntry = realpathSync(resolve(dirname(fileURLToPath(import.meta.url)), '../../vendor/picomatch/index.js'))
    for (const dependency of ['vite', 'vitest', 'tinyglobby', 'fdir']) {
      const resolveFromDependency = createRequire(require.resolve(dependency))
      expect(realpathSync(resolveFromDependency.resolve('picomatch')), dependency).toBe(patchedEntry)
    }
  })

  it.each([
    ['+(*.js)', 'page.js'],
    ['*(prefix*)', 'prefix-file'],
    ['src/+(*.js)', 'src/page.js'],
    ['+(assets/**)', 'assets/icons/edit.svg'],
    ['+(!(*.map))', 'page.js'],
  ])('treats %s as a literal instead of compiling nested repetition', (pattern, ordinaryInput) => {
    for (const entry of [matcher, require('picomatch/posix')]) {
      for (const options of [{}, { maxExtglobRecursion: false, fastpaths: false }]) {
        const regex = entry.makeRe(pattern, options)
        expect(regex.test(pattern)).toBe(true)
        expect(regex.test(ordinaryInput)).toBe(false)
      }
    }
  })

  it.each([
    ['src/**/view?.jsx', 'src/pages/view1.jsx', 'src/pages/view1.css'],
    ['assets/!(*.map)', 'assets/main.js', 'assets/main.map'],
    ['+(app|test).js', 'apptest.js', 'appother.js'],
    ['+([*])', '***', 'file'],
    ['+(file\\*)', 'file*file*', 'file.txt'],
    ['+(*(a)|*(b))', 'aabba', 'abc'],
  ])('keeps matching safe patterns such as %s', (pattern, included, excluded) => {
    const regex = matcher.makeRe(pattern)
    expect(regex.test(included)).toBe(true)
    expect(regex.test(excluded)).toBe(false)
  })
})
