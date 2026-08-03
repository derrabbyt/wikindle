import { DatePipe } from "@angular/common";
import { Component, OnInit, signal } from "@angular/core";
import { API_BASE_URL } from "./api-config";

interface DemoItem {
  id: number;
  name: string;
  created_at: string;
}

interface ItemsResponse {
  database: string;
  source: string;
  items: DemoItem[];
}

@Component({
  selector: "app-root",
  imports: [DatePipe],
  templateUrl: "./app.html",
  styleUrl: "./app.css",
})
export class App implements OnInit {
  readonly items = signal<DemoItem[]>([]);
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);
  readonly source = signal("");

  ngOnInit(): void {
    void this.loadItems();
  }

  async loadItems(): Promise<void> {
    this.loading.set(true);
    this.error.set(null);

    try {
      const response = await fetch(`${API_BASE_URL}/api/items`, {
        method: "GET",
        headers: {
          Accept: "application/json",
        },
      });

      if (!response.ok) {
        throw new Error(`API returned HTTP ${response.status}`);
      }

      const payload = (await response.json()) as ItemsResponse;

      this.items.set(payload.items);
      this.source.set(payload.source);
    } catch (error: unknown) {
      console.error(error);
      this.error.set("The backend or database could not be reached.");
    } finally {
      this.loading.set(false);
    }
  }
}