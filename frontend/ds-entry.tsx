// design-sync 번들 엔트리 — claude.ai/design `TheSC AX Design System` 에 올리는 디자인 부품만 내보낸다.
// 화면·features·shell·api 는 디자인 부품이 아니라 여기 없다. 부품이 늘면 여기에 한 줄 추가한다.
//
// 바퀴 10 이 `src/` 를 도메인 구조로 갈랐다 — 디자인 부품은 전부 `src/ds/` 한 곳에 산다.
// 이 파일의 목록 = `src/ds/` 의 비-테스트 파일 22개(+ `ds/icons/Icon`)가 내보내는 것 전부다.

export { Icon } from "./src/ds/icons/Icon";
export type { IconName } from "./src/ds/icons/Icon";

export { Avatar, AvatarEmpty } from "./src/ds/Avatar";
export type { AvatarSize, AvatarProps } from "./src/ds/Avatar";

export { Badge } from "./src/ds/Badge";
export type { BadgeTone } from "./src/ds/Badge";

export { Button, ButtonGroup, IconButton } from "./src/ds/Button";
export type { ButtonVariant, ButtonTone } from "./src/ds/Button";

export { Chip, ChipBar, ChipRow, ChipToggle } from "./src/ds/Chip";

export { DataTable, Th, Td, TrOpenable } from "./src/ds/DataTable";
export type { CellAlign } from "./src/ds/DataTable";

export { SegmentedControl, Tabs } from "./src/ds/SegmentedControl";
export type { ChoiceOption } from "./src/ds/SegmentedControl";

export { GutterList } from "./src/ds/GutterList";
export type { GutterRow } from "./src/ds/GutterList";

export { StatusNote } from "./src/ds/StatusNote";

export { DropZone } from "./src/ds/DropZone";

export { FileList } from "./src/ds/FileList";
export type { FileRow } from "./src/ds/FileList";

export { Popover } from "./src/ds/Popover";
export { Empty, EmptyValue } from "./src/ds/Empty";
export type { EmptyVariant } from "./src/ds/Empty";
export { Skeleton } from "./src/ds/Skeleton";
export { ProgressBar } from "./src/ds/ProgressBar";

export { Drawer, Modal, ConfirmModal, Toast, useEscape } from "./src/ds/Modal";

export { Checkbox, CheckboxBox, FieldMessage } from "./src/ds/FormControls";

export { DateField } from "./src/ds/DateField";
export { DatePicker } from "./src/ds/DatePicker";
export type { DatePickerLabels } from "./src/ds/DatePicker";

export { Select, MultiSelect, SelectOptionPanel } from "./src/ds/Select";
export type { SelectOption, SelectFooterAction, SelectTriggerState, SelectTriggerProps, SelectLabels } from "./src/ds/Select";

export { TimeField, TimeRangeField, formatTime, parseTime, timeSlots } from "./src/ds/TimeField";
export type { TimeFieldLabels } from "./src/ds/TimeField";

export { TimeChip } from "./src/ds/TimeChip";
export { MinWidthNotice } from "./src/ds/MinWidthNotice";
