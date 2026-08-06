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
    const url = `${API_BASE_URL}${path}`;
    let response: Response;

    try {
      response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch (error: unknown) {
      // Only a genuine transport failure reaches here: DNS, TLS, CORS or no
      // network. Anything with a status code takes the path below.
      console.error(`wikindle: could not reach ${url}`, error);
      return {
        kind: "error",
        message: `Could not reach ${host()}. Check your connection and try again.`,
      };
    }

    // Parsed defensively and *after* checking the status: when the API is down,
    // Cloudflare answers with an HTML error page, and parsing that as JSON used
    // to throw and be reported as though the server were unreachable.
    const payload = await readJson(response);

    if (!response.ok) {
      if (payload?.error) {
        return { kind: "error", message: payload.error };
      }
      console.error(`wikindle: ${url} returned ${response.status}`);
      return {
        kind: "error",
        message:
          response.status >= 500
            ? `${host()} returned ${response.status}. It may be restarting — try again in a minute.`
            : `${host()} returned ${response.status}. Please try again.`,
      };
    }

    return { kind: "ok", message: payload?.message ?? "Done." };
  }
}

interface ApiPayload {
  message?: string;
  error?: string;
}

function host(): string {
  try {
    return new URL(API_BASE_URL).host;
  } catch {
    return "the server";
  }
}

async function readJson(response: Response): Promise<ApiPayload | null> {
  try {
    return (await response.json()) as ApiPayload;
  } catch {
    return null;
  }
}
