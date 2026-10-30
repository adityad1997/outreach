import { usePlainVariableAutocomplete } from './VariableAutocomplete';

/**
 * Text input or textarea with {{ variable autocomplete.
 */
export default function VariableTextInput({
  value,
  onChange,
  customKeys = [],
  multiline = false,
  className = '',
  ...rest
}) {
  const { menu, bind } = usePlainVariableAutocomplete({
    value: value ?? '',
    onChange,
    customKeys,
  });

  const {
    onKeyDown: restKeyDown,
    onKeyUp: restKeyUp,
    onClick: restClick,
    onSelect: restSelect,
    ...inputRest
  } = rest;

  const shared = {
    ...inputRest,
    className,
    ref: bind.ref,
    value: bind.value,
    onChange: bind.onChange,
    onKeyUp: e => {
      bind.onKeyUp?.(e);
      restKeyUp?.(e);
    },
    onClick: e => {
      bind.onClick?.(e);
      restClick?.(e);
    },
    onSelect: e => {
      bind.onSelect?.(e);
      restSelect?.(e);
    },
    onKeyDown: e => {
      bind.onKeyDown?.(e);
      if (!e.defaultPrevented) restKeyDown?.(e);
    },
  };

  return (
    <>
      {multiline ? <textarea {...shared} /> : <input type="text" {...shared} />}
      {menu}
    </>
  );
}
