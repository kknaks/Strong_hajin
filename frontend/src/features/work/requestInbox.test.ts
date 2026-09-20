import { expect, it } from "vitest";
import { requestInboxItems } from "./requestInbox";
import { requestInboxFixture } from "../../shell/fixtures/requestInbox";

it("unions assignee inbox and CC requests once, using server category before role fallback", () => {
  const [work, , reference] = requestInboxFixture;
  const result = requestInboxItems(
    [{ ...work, cc_member_ids: ["me"] }, { ...reference, assignee_id: "me" }],
    [{ ...work, category: undefined, cc_member_ids: ["me"] }, { ...reference, category: undefined }],
    "me",
  );
  expect(result.map((item) => [item.request_id, item.category])).toEqual([[work.request_id, "work"], [reference.request_id, "reference"]]);
});

it("uses only received requests and CC relationships when category is absent", () => {
  const [work, , reference] = requestInboxFixture;
  expect(requestInboxItems([{ ...work, category: undefined }], [
    { ...reference, category: undefined },
    { ...work, request_id: "not-cc", assignee_id: "another", category: undefined },
  ], "me").map((item) => item.category)).toEqual(["reference", "work"]);
});
