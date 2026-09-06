import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", () => ({ getAuthProviders: vi.fn(), login: vi.fn() }));

import * as api from "./api";
import { LoginPage } from "./LoginPage";

const profile = { member_id: "mina", display_name: "민아 (구성원)", organizations: [], roles: [], capabilities: [] };

describe("로그인", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("signs in with an address and a password, and never lists accounts unless the server offers them", async () => {
    vi.mocked(api.getAuthProviders).mockResolvedValue({ local: true, oidc: false } as never);
    vi.mocked(api.login).mockResolvedValue(profile as never);
    const onLoggedIn = vi.fn();
    render(<LoginPage onLoggedIn={onLoggedIn} />);

    await screen.findByLabelText("이메일");
    expect(screen.queryByLabelText("로컬 데모 계정")).toBeNull();
    fireEvent.change(screen.getByLabelText("이메일"), { target: { value: "mina@scax.example" } });
    fireEvent.change(screen.getByLabelText("비밀번호"), { target: { value: "scax-demo-1234" } });
    fireEvent.click(screen.getByRole("button", { name: "로그인" }));

    await waitFor(() => expect(api.login).toHaveBeenCalledWith("mina@scax.example", "scax-demo-1234"));
    await waitFor(() => expect(onLoggedIn).toHaveBeenCalledWith(profile));
  });

  it("offers the local demo accounts as a shortcut that still goes through a real sign-in", async () => {
    vi.mocked(api.getAuthProviders).mockResolvedValue({
      local: true,
      oidc: false,
      demo_password: "scax-demo-1234",
      demo_accounts: [
        { member_id: "yuna", email: "yuna@scax.example", display_name: "유나 (대표)" },
        { member_id: "mina", email: "mina@scax.example", display_name: "민아 (구성원)" },
      ],
    } as never);
    vi.mocked(api.login).mockResolvedValue(profile as never);
    const onLoggedIn = vi.fn();
    render(<LoginPage onLoggedIn={onLoggedIn} />);

    const shortcuts = await screen.findByLabelText("로컬 데모 계정");
    expect(within(shortcuts).getAllByRole("listitem")).toHaveLength(2);
    fireEvent.click(within(shortcuts).getByText("유나 (대표)"));

    // The same credentials the form would have sent, through the same call — and visible in the form afterwards.
    await waitFor(() => expect(api.login).toHaveBeenCalledWith("yuna@scax.example", "scax-demo-1234"));
    expect((screen.getByLabelText("이메일") as HTMLInputElement).value).toBe("yuna@scax.example");
    await waitFor(() => expect(onLoggedIn).toHaveBeenCalledWith(profile));
  });

  it("says what went wrong when a shortcut is refused, rather than pretending it signed in", async () => {
    vi.mocked(api.getAuthProviders).mockResolvedValue({
      local: true,
      oidc: false,
      demo_password: "wrong",
      demo_accounts: [{ member_id: "yuna", email: "yuna@scax.example", display_name: "유나 (대표)" }],
    } as never);
    vi.mocked(api.login).mockRejectedValue(new Error("이메일 또는 비밀번호가 올바르지 않습니다."));
    const onLoggedIn = vi.fn();
    render(<LoginPage onLoggedIn={onLoggedIn} />);

    fireEvent.click(await screen.findByText("유나 (대표)"));
    expect((await screen.findByRole("alert")).textContent).toContain("이메일 또는 비밀번호가 올바르지 않습니다.");
    expect(onLoggedIn).not.toHaveBeenCalled();
  });
});
