import { Component, signal } from "@angular/core";
import { FormsModule } from "@angular/forms";
import { API_BASE_URL } from "./api-config";

type Outcome = { kind: "ok" | "error"; message: string } | null;

@Component({
  selector: "app-root",
  imports: [FormsModule],
  templateUrl: "./app.html",
  styleUrl: "./app.css",
})
export class App {
  readonly kindleAddress = signal("");
  readonly contactEmail = signal("");
  readonly extraAddress = signal("");
  readonly extraUrl = signal("");

  readonly signupBusy = signal(false);
  readonly signupOutcome = signal<Outcome>(null);
  readonly extraBusy = signal(false);
  readonly extraOutcome = signal<Outcome>(null);

  /**
   * Captured silently, so per-timezone delivery stays possible later without
   * having to ask every existing subscriber for it.
   */
  private readonly timezone =
    Intl.DateTimeFormat().resolvedOptions().timeZone ?? null;

  async subscribe(): Promise<void> {
    this.signupBusy.set(true);
    this.signupOutcome.set(null);
    try {
      this.signupOutcome.set(
        await this.post("/api/subscribe", {
          kindle_address: this.kindleAddress().trim(),
          contact_email: this.contactEmail().trim(),
          timezone: this.timezone,
        }),
      );
    } finally {
      this.signupBusy.set(false);
    }
  }

  async sendExtra(): Promise<void> {
    this.extraBusy.set(true);
    this.extraOutcome.set(null);
    try {
      const url = this.extraUrl().trim();
      this.extraOutcome.set(
        await this.post("/api/on-demand", {
          kindle_address: this.extraAddress().trim(),
          url: url.length > 0 ? url : null,
        }),
      );
    } finally {
      this.extraBusy.set(false);
    }
  }

  private async post(path: string, body: unknown): Promise<Outcome> {
    try {
      const response = await fetch(`${API_BASE_URL}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = (await response.json()) as {
        message?: string;
        error?: string;
      };

      if (!response.ok) {
        return {
          kind: "error",
          message: payload.error ?? "Something went wrong.",
        };
      }
      return { kind: "ok", message: payload.message ?? "Done." };
    } catch {
      return { kind: "error", message: "Could not reach the server." };
    }
  }
}
