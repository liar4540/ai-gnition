import { Component } from "react";
import { Alert, Box, Button } from "@mui/material";

// Shows the error instead of a blank page if a part of the UI crashes.
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error("UI error:", error, info?.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <Box sx={{ p: 2 }}>
        <Alert severity="error" action={<Button color="inherit" size="small" onClick={() => this.setState({ error: null })}>Try again</Button>}>
          {this.props.label || "This part of the page"} hit an error: {String(this.state.error?.message || this.state.error)}
        </Alert>
      </Box>
    );
  }
}
