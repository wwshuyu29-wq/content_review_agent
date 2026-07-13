import type { ContentVersion } from "../api";

interface DiffViewProps {
  original: ContentVersion;
  proposed: ContentVersion;
  editable?: boolean;
  title: string;
  body: string;
  onTitleChange?: (value: string) => void;
  onBodyChange?: (value: string) => void;
}

export default function DiffView({
  original,
  proposed,
  editable = false,
  title,
  body,
  onTitleChange,
  onBodyChange,
}: DiffViewProps) {
  return (
    <div className="diff-view">
      <section className="diff-pane original">
        <div className="section-label">原始版本 V{original.version}</div>
        <h4>{original.title}</h4>
        <div className="diff-body">{original.body}</div>
      </section>
      <section className="diff-pane proposed">
        <div className="section-label">AI 建议 V{proposed.version}</div>
        {editable ? (
          <>
            <label htmlFor="edited-title">确认标题</label>
            <input id="edited-title" type="text" value={title} onChange={(event) => onTitleChange?.(event.target.value)} />
            <label htmlFor="edited-body">确认正文</label>
            <textarea id="edited-body" rows={8} value={body} onChange={(event) => onBodyChange?.(event.target.value)} />
          </>
        ) : (
          <>
            <h4>{proposed.title}</h4>
            <div className="diff-body">{proposed.body}</div>
          </>
        )}
      </section>
    </div>
  );
}
