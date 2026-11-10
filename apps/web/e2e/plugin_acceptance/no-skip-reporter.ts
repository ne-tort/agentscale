import type { FullConfig, FullResult, Reporter, Suite, TestCase, TestResult } from "@playwright/test/reporter";

/** Acceptance is mandatory: zero collection and every skip are hard failures. */
export default class PluginAcceptanceNoSkipReporter implements Reporter {
  private suite: Suite | undefined;
  private collected = 0;
  private readonly skipped = new Set<string>();

  onBegin(_config: FullConfig, suite: Suite): void {
    this.suite = suite;
    this.collected = suite.allTests().length;
    if (this.collected === 0) {
      process.exitCode = 1;
      console.error("PLUGIN_ACCEPTANCE gate: collected=0");
    }
  }

  onTestEnd(test: TestCase, result: TestResult): void {
    if (result.status === "skipped" || test.outcome() === "skipped") this.skipped.add(test.id);
  }

  onEnd(_result: FullResult): void {
    const skipped = this.suite
      ? this.suite.allTests().filter((test) => test.outcome() === "skipped" || this.skipped.has(test.id)).length
      : this.skipped.size;
    console.log(`PLUGIN_ACCEPTANCE gate: collected=${this.collected} skipped=${skipped}`);
    if (this.collected === 0 || skipped !== 0) {
      process.exitCode = 1;
      console.error(`PLUGIN_ACCEPTANCE gate failed: required collected>0 and skipped=0, got ${this.collected}/${skipped}`);
    }
  }
}
