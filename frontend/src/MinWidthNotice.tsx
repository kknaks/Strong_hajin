import { Icon } from "./Icon";
import { minWidthNotice } from "./labels";

/**
 * Legacy design-system export retained for import compatibility.
 * The application no longer mounts it, and current styles keep it hidden for older consumers.
 */
export function MinWidthNotice() {
  return (
    <div className="min-width-notice" role="alert">
      <Icon name="alert" size={20} />
      <b>{minWidthNotice.title}</b>
      <p>{minWidthNotice.description}</p>
    </div>
  );
}
