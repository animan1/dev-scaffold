import { render, screen, waitFor } from "@testing-library/react";
import { App } from "../App";

global.fetch = vi.fn().mockResolvedValue({
  json: async () => ({ status: "ok" }),
}) as unknown as typeof fetch;

it("renders API status", async () => {
  render(<App />);
  await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("ok"));
});
